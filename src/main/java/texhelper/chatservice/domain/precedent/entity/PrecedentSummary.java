package texhelper.chatservice.domain.precedent.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Getter;
import lombok.NoArgsConstructor;
import texhelper.chatservice.common.entity.Timestamped;

@Entity
@Table(name = "precedent")
@Getter
@AllArgsConstructor
@NoArgsConstructor
public class PrecedentSummary extends Timestamped {

    @Column(nullable= false)
    private String caseNo; //사건번호

    @Column
    private String dataSource; //데이터 출처명

    @Column
    private String caseTypeCode; //사건 종류 코드

    @Column
    private String  caseTypeName; //사건종류명

    @Column
    private String sentencing; //선고

    @Column
    private String sentencingDate; //선고일자

    @Column
    private String courtName; // 법원명

    @Id
    @Column (nullable= false)
    private String precedentNo; // 판례 일련번호

    @Column
    private String judgmentType; // 판결유형

    @Column (nullable= false)
    private String caseName; // 사건명
}
