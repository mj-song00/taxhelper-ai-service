package taxhelper.chatservice.domain.precedent.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import taxhelper.chatservice.domain.precedent.entity.PrecedentSummary;

public interface PrecedentSummaryRepository extends JpaRepository <PrecedentSummary, String> {
}
