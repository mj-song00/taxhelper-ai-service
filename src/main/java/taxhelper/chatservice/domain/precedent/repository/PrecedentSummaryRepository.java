package taxhelper.chatservice.domain.precedent.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import taxhelper.chatservice.domain.precedent.entity.PrecedentSummary;

import java.util.List;

public interface PrecedentSummaryRepository extends JpaRepository <PrecedentSummary, String> {

    @Query("SELECT p.precedentNo FROM  PrecedentSummary p")
    List<String> findAllPrecedentNo();
}
